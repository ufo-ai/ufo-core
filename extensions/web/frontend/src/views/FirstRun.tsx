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
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { Notice, Panel, PanelSkeleton, usePanelRead } from "@/kernel/panel";
import { Frame, Head } from "@/views/Frame";
import { AgentIcon } from "@/lib/agentIcon";
import { BASE, postAction, postIntent, postObjectAction } from "@/lib/api";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import { ConsentLink, openConsentWindow } from "@/lib/consent";
import { chatSurface } from "@/lib/mainAgent";
import { openConversation } from "@/lib/turnStream";
import type { ActionView, Agent, Member } from "@/lib/types";
import { CONNECT_INSTALLS, Connect, ConnectSurfaces } from "@/views/Surfaces";

type ProviderTile = { name: string; label: string; summary: string; group: string };

type Connector = ProviderTile & { installed: boolean };

export type FirstRunPayload = {
  providers: ProviderTile[];
  connectors: Connector[];
  actions: { member: ActionView[]; memory: ActionView[]; enrichment_profile: ActionView[] };
  model_key_held: boolean;
  workspace_domain: string | null;
};

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

const WEBSITE_STEP = "website";
const BUSINESS_STEP = "business";
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

const ROLE_JOIN = " / ";

function saidRoles(roles: Role[], otherRole: string): string {
  const named = roles.map((role) => (role === OTHER_ROLE ? otherRole.trim() : role));
  return named.every(Boolean) ? named.join(ROLE_JOIN) : "";
}

function suggestedRole(profile: Profile): Role {
  const levels = (profile.job_title_levels ?? "").split(",").map((level) => level.trim());
  if (levels.some((level) => FOUNDER_LEVELS.has(level))) return DEFAULT_ROLE;
  return ROLE_BY_CLASS[profile.job_title_role ?? ""] ?? DEFAULT_ROLE;
}

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

const FIRST_TASK = "Set up my first task: a daily competitive analysis.";

function opening(business: string): string {
  const said = (text: string) => text.trim().replace(/[.!?]+$/, "");
  return "I just set up this workspace. My business: " + said(business) + ". " + FIRST_TASK;
}

export const FIRST_RUN_READ = "/workspace/first-run";

const RECORD_FIRST_RUN_ACTION = "record_first_run";

/** The install happens on the provider's own pages, so nothing here can say when it lands — the wait is
 *  measured in the seconds the member spends over there. */
export const WATCH_MS = 3_000;

type PoolPayload = { connections: { provider: string }[] };

const POOL_READ = "/connections";

const TOOLS_BY_ROLE: Record<Role, string[]> = {
  Founder: ["gmail", "googlecalendar", "notion", "github", "stripe", "hubspot"],
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

function offered(roles: Role[], providers: ProviderTile[]): ProviderTile[] {
  const votes = new Map<string, number>();
  for (const role of roles) {
    for (const name of TOOLS_BY_ROLE[role]) votes.set(name, (votes.get(name) ?? 0) + 1);
  }
  const named = [...votes.keys()].sort((a, b) => (votes.get(b) ?? 0) - (votes.get(a) ?? 0));
  const rest = providers.filter((tile) => tile.name !== SLACK_STEP && !votes.has(tile.name));
  return [
    ...named
      .map((name) => providers.find((tile) => tile.name === name))
      .filter((tile): tile is ProviderTile => tile !== undefined),
    ...rest,
  ];
}

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

const DEFAULT_GOAL = GOALS[0].label;

const OTHER_ASKS =
  "Start by saying what you can already read about it from whatever access I have granted, and " +
  "the first step you would take. Do not guess what you could not read — say so. Ask me at most " +
  "one thing.";

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

/** The action bounds a body, so the goals fall away before the business does: a member with no goals
 *  still has a workspace that knows them. */
export function firstRunRecorded(
  business: string,
  website: string,
  role: string,
  goals: string[],
  budget: number,
): string {
  const said = (text: string) => text.trim().replace(/[.!?]+$/, "");
  const site = website.trim() ? " Their website: " + website.trim() + "." : "";
  const who = said(business) + "." + site + " Their role: " + role + ".";
  for (let named = goals.length; named > 0; named -= 1) {
    const body = who + " Their goals: " + goals.slice(0, named).join(", ") + ".";
    if (body.length <= budget) return body;
  }
  return who.slice(0, budget);
}

type Answers = {
  business: string;
  businessWritten: boolean;
  website: string;
  websiteWritten: boolean;
  profile: Profile | null;
  profilePending: boolean;
  roles: Role[];
  rolePicked: boolean;
  otherRole: string;
  goals: string[];
  otherGoal: string;
  tools: string[];
  declined: boolean;
};

const ANSWERS_PREFIX = "ufo.first-run.";

function answersKey(member: Member): string {
  return ANSWERS_PREFIX + (member.workspace_id ?? member.email);
}

function freshAnswers(): Answers {
  return {
    business: "",
    businessWritten: false,
    website: "",
    websiteWritten: false,
    profile: null,
    profilePending: false,
    roles: [DEFAULT_ROLE],
    rolePicked: false,
    otherRole: "",
    goals: [DEFAULT_GOAL],
    otherGoal: "",
    tools: [],
    declined: false,
  };
}

function readAnswers(member: Member): Answers {
  const fresh = freshAnswers();
  try {
    const held = globalThis.sessionStorage?.getItem(answersKey(member));
    return held ? { ...fresh, ...(JSON.parse(held) as Partial<Answers>) } : fresh;
  } catch {
    return fresh;
  }
}

/** A browser that refuses the write costs the member their answers on a reload, never the run. */
function holdAnswers(member: Member, answers: Answers | null): void {
  try {
    const held = globalThis.sessionStorage;
    if (answers === null) held?.removeItem(answersKey(member));
    else held?.setItem(answersKey(member), JSON.stringify(answers));
  } catch {
    return;
  }
}

function revealedSteps(payload: FirstRunPayload, declined: boolean, tools: boolean): string[] {
  const slack = payload.connectors.some((row) => row.name === SLACK_STEP);
  return [
    WEBSITE_STEP,
    BUSINESS_STEP,
    POSITION_STEP,
    ...(tools ? [TOOLS_STEP] : []),
    GOALS_STEP,
    ...(slack ? [SLACK_STEP] : []),
    ...(slack && declined ? [SURFACES_STEP] : []),
  ];
}

function Land({ step, onStep }: { step: string; onStep: (step: string) => void }) {
  useEffect(() => onStep(step), [step, onStep]);
  return null;
}

export const BUILD_STEP_MS = 700;

/** A local slot has no Slack app to install, so a dev host offers a way onto the success screen. */
const LOCAL_DEV = location.hostname === "localhost" || location.hostname.endsWith(".localhost");

const SLACK_POINTS = [
  "Mention @ufo or send it a DM.",
  "It replies, remembers, and works with your team.",
  "One install for the whole workspace.",
];

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

const STEP_COLUMN = "flex w-(--container-answer) max-w-full flex-col items-start gap-6xl";

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

function Failed({ message, onToast }: { message: string; onToast: (title: string) => void }) {
  useEffect(() => onToast(message), [message, onToast]);
  return null;
}

const CHOICES = "grid w-full grid-cols-[repeat(auto-fit,minmax(var(--container-choice),1fr))] gap-2xs";

const CHOICE = cn(
  "group flex h-10 items-center justify-between gap-sm rounded-(--radius-answer)",
  "border border-transparent bg-fill px-2xl text-start text-label whitespace-nowrap text-ink",
  "hover:bg-fill-strong focus-visible:outline-none",
  "data-[state=on]:border-link data-[state=on]:bg-transparent data-[state=on]:text-link",
  "data-[state=on]:hover:bg-transparent",
);

const ANSWER_CARD = "flex w-full flex-col gap-2xl rounded-(--radius-answer) bg-fill p-lg";

const ANSWER_BOX = cn(
  "w-full resize-none border-0 bg-transparent p-0 font-sans text-label text-field-ink",
  "placeholder:text-ink-faint focus-visible:outline-none",
);

/** Every line is trimmed to its caps, so a gap between two lines measures from the letters rather than
 *  from the half-leading around them. */
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
                  <span className={cn("text-label font-medium text-surface", CAP_TRIM)} aria-hidden>
                    {index + 1}
                  </span>
                </span>
                <div className="flex min-w-0 flex-1 flex-col gap-2xl">
                  <h2 className={cn("m-0 text-subtitle font-medium text-surface", CAP_TRIM)}>
                    {point.title}
                  </h2>
                  <p className={cn("m-0 text-subtitle text-ink-quiet", CAP_TRIM)}>
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

/** `hidden` says the deploy withholds the app from every list the portal draws, never that the app is
 *  unbuilt — the workspace holds it and a member arriving on its address opens it. */
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
  const [connecting, setConnecting] = useState(false);
  const pool = usePanelRead<PoolPayload>(connecting ? POOL_READ : null, 0, WATCH_MS);
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [building, setBuilding] = useState(false);
  const [threads, setThreads] = useState<string[]>([]);
  /* The promise is held rather than the id, so the end of the run waits on the founding it started
     rather than founding a second one. */
  const founding = useRef<Promise<string | null> | null>(null);
  const [thread, setThread] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(SILENT);
  const [profileRead, setProfileRead] = useState(0);
  /* Held across renders, because the failed panel raises its sentence from an effect keyed on this
     function: a new one every render would raise the sentence again on every render it caused. */
  const refuse = useCallback((title: string) => setToast({ title }), []);
  useEffect(() => holdAnswers(member, answers), [member, answers]);
  const answer = (patch: Partial<Answers>) => setAnswers((held) => ({ ...held, ...patch }));
  const close = () => {
    holdAnswers(member, null);
    onClose();
  };
  const { business, website, profile, roles, otherRole, goals, otherGoal, tools } = answers;
  const declined = asked === SURFACES_STEP || answers.declined;
  const acceptProfile = useCallback((own: Profile) => {
    setAnswers((held) => {
      const learned = describes(own);
      return {
        ...held,
        profile: own,
        profilePending: false,
        roles: held.rolePicked ? held.roles : [suggestedRole(own)],
        business: held.businessWritten || !learned ? held.business : learned,
      };
    });
  }, []);
  const domain = state.phase === "ready" ? state.payload.workspace_domain : null;
  useEffect(() => {
    if (!domain) return;
    setAnswers((held) => (held.websiteWritten ? held : { ...held, website: domain }));
  }, [domain]);
  const profileWatch = answers.profilePending ? (
    <ProfileWatch key={profileRead} email={member.email} onProfile={acceptProfile} />
  ) : null;

  if (asked === undefined) {
    return <Welcome onStart={() => onStep(WEBSITE_STEP)} onClose={close} />;
  }
  if (building) {
    return (
      <>
        <Building
          company={profile?.status === "matched" ? profile.company_name : null}
          apps={agents.filter((row) => row.app && !row.main)}
          assistant={agent}
          threads={threads}
          onClose={() => onDone(thread)}
          onDone={() => onDone(thread)}
        />
        <Toast state={toast} onDone={() => setToast(SILENT)} />
      </>
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
          const offers = offered(roles, payload.providers);
          const revealed = revealedSteps(payload, declined, offers.length > 0);
          const at = Math.max(0, revealed.indexOf(asked));
          const step = revealed[at];
          const held = slack ? slack.installed : false;
          const installed = step === SLACK_STEP && (held || connected);
          const said = saidRoles(roles, otherRole);
          const write = payload.actions.memory.find(
            (view) => view.name === RECORD_FIRST_RUN_ACTION,
          );
          const speaks = chatSurface(agents) ?? agent;
          const kickOff = () => {
            founding.current ??= openConversation(speaks.id, opening(business)).then((founded) => {
              setThread(founded);
              return founded;
            });
          };
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
              body: firstRunRecorded(
                business,
                website,
                said,
                picked.map((goal) => goal.said),
                budget,
              ),
            });
            if (!outcome.applied) {
              setBusy(false);
              refuse(outcome.message);
              return;
            }
            const opened: string[] = [];
            const missed: string[] = [];
            for (const goal of picked) {
              const founded = await openConversation(speaks.id, goalOpening(business, said, goal));
              (founded ? opened : missed).push(goal.label);
            }
            kickOff();
            await founding.current;
            setThreads(opened);
            if (missed.length) {
              refuse(`No thread started for ${missed.join(", ")}. Ask for it in chat.`);
            }
            setBusy(false);
            setBuilding(true);
            holdAnswers(member, null);
          };
          const advance = () => {
            if (step === BUSINESS_STEP) kickOff();
            if (step === POSITION_STEP) answer({ rolePicked: true });
            if (step === TOOLS_STEP && tools.length && !connecting) return setConnecting(true);
            return at + 1 < revealed.length ? onStep(revealed[at + 1]) : void finish();
          };
          const back = () => {
            if (step === TOOLS_STEP && connecting) return setConnecting(false);
            return onStep(at ? revealed[at - 1] : undefined);
          };
          const confirmWebsite = async () => {
            if (busy) return;
            if (!confirm) return advance();
            setBusy(true);
            const outcome = await postAction(agent.id, confirm.call, { website: website.trim() });
            if (!outcome.applied) {
              setBusy(false);
              refuse(outcome.message);
              return;
            }
            setBusy(false);
            setAnswers((held) => ({
              ...held,
              profile: null,
              profilePending: Boolean(website.trim()),
              roles: held.rolePicked ? held.roles : [DEFAULT_ROLE],
              business: held.businessWritten ? held.business : "",
            }));
            setProfileRead((read) => read + 1);
            advance();
          };
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
                      placeholder="What does your business do, and who is it for?"
                      value={business}
                      onChange={(event) =>
                        answer({ business: event.target.value, businessWritten: true })
                      }
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
                  <h1 className="m-0 text-subtitle font-medium text-ink">
                    What’s the website for your business?
                  </h1>
                  <div className="relative w-full">
                    <Input
                      surface="answer"
                      aria-label="Website"
                      autoFocus
                      inputMode="url"
                      autoComplete="url"
                      placeholder="company.com"
                      value={website}
                      onChange={(event) =>
                        answer({ website: event.target.value, websiteWritten: true })
                      }
                    />
                    {website ? (
                      <Button
                        variant="mark"
                        size="glyph"
                        aria-label="Clear"
                        className="absolute top-1/2 right-lg -translate-y-1/2"
                        onClick={() => answer({ website: "", websiteWritten: true })}
                      >
                        <IconX stroke={1.5} aria-hidden />
                      </Button>
                    ) : null}
                  </div>
                </Step>
              ) : null}
              {step === POSITION_STEP ? (
                <Step onBack={back} onNext={advance} nextDisabled={!roles.length || !said}>
                  <h1 className="m-0 text-subtitle font-medium text-ink">
                    What is your role at the business?
                  </h1>
                  <ToggleGroup
                    aria-label="Role"
                    className={CHOICES}
                    value={roles}
                    onValueChange={(picked) => {
                      answer({
                        roles: ROLES.filter((option) => picked.includes(option)),
                        rolePicked: true,
                      });
                    }}
                  >
                    {ROLES.map((option) => (
                      <ToggleGroupItem
                        key={option}
                        value={option}
                        autoFocus={option === roles[0]}
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
                  </ToggleGroup>
                  {roles.includes(OTHER_ROLE) ? (
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
                  <h1 className="m-0 text-subtitle font-medium text-ink">
                    Which tools do you work in?
                  </h1>
                  <ToggleGroup
                    aria-label="Tools"
                    className={CHOICES}
                    value={tools}
                    onValueChange={(picked) => answer({ tools: picked })}
                  >
                    {offers.map((tile) => (
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
                  <h1 className="m-0 text-subtitle font-medium text-ink">
                    Connect the tools you picked
                  </h1>
                  <ul className="m-0 flex w-full list-none flex-col gap-2xs p-0">
                    {offers
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
                  <h1 className="m-0 text-subtitle font-medium text-ink">
                    What is top of mind right now?
                  </h1>
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
                  <h1 className="m-0 text-subtitle font-medium text-ink">
                    Get UFO everywhere you work
                  </h1>
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
      {profileWatch}
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </>
  );
}

function ProfileWatch({
  email,
  onProfile,
}: {
  email: string;
  onProfile: (profile: Profile) => void;
}) {
  const state = usePanelRead<{ objects: Profile[] }>(
    `${PROFILE_READ}?email=${encodeURIComponent(email)}`,
    0,
    WATCH_MS,
  );
  const rows = state.phase === "ready" ? state.payload.objects : null;
  useEffect(() => {
    if (rows === null) return;
    const profile = rows.find((row) => row.name === email);
    if (profile) onProfile(profile);
  }, [email, onProfile, rows]);
  return null;
}

/** The broker verb ends its turn on the handoff rather than in its answer, so the link is the turn's
 *  own `connect` frame and this waits for it. */
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

/** The consent window opens on the press, before the round trip that mints the link, because a window
 *  opened after it has lost the gesture the browser opens one for. */
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
